from datetime import UTC, datetime

from timeline_sync.visit_deriver import Visit, derive_visits, merge_consecutive_visits, merge_nearby_visits

ENTITY = "device_tracker.phone"


def dt(hour: int, minute: int = 0) -> str:
    return datetime(2024, 1, 15, hour, minute, tzinfo=UTC).isoformat()


def make_state(
    state: str,
    hour: int,
    minute: int = 0,
    lat: float = 0.0,
    lng: float = 0.0,
    geocoded_location: str | None = None,
    location: list | None = None,
) -> dict:
    attrs: dict = {}
    if location is not None:
        attrs["location"] = location
    else:
        attrs["latitude"] = lat
        attrs["longitude"] = lng
    if geocoded_location:
        attrs["geocoded_location"] = geocoded_location
    return {"state": state, "last_changed": dt(hour, minute), "attributes": attrs}


def make_visit(
    place: str,
    hour_start: int,
    hour_end: int | None,
    lat: float = 0.0,
    lng: float = 0.0,
    minute_start: int = 0,
    minute_end: int = 0,
) -> Visit:
    return Visit(
        visit_id=f"id_{place}_{hour_start}_{minute_start}",
        place_name=place,
        start=datetime(2024, 1, 15, hour_start, minute_start, tzinfo=UTC),
        end=datetime(2024, 1, 15, hour_end, minute_end, tzinfo=UTC) if hour_end is not None else None,
        lat=lat,
        lng=lng,
        source="ha_zone",
    )


class TestDeriveVisits:
    def test_empty_history_returns_empty(self):
        result = derive_visits([], ENTITY, datetime.now(UTC))
        assert result == []

    def test_single_state_produces_ongoing_visit(self):
        history = [make_state("home", 8, lat=37.7, lng=-122.4)]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert len(visits) == 1
        assert visits[0].place_name == "home"
        assert visits[0].end is None  # ongoing
        assert visits[0].source == "ha_zone"

    def test_two_different_states_produce_two_visits(self):
        history = [
            make_state("home", 8),
            make_state("work", 9),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert len(visits) == 2
        assert visits[0].place_name == "home"
        assert visits[0].end == datetime(2024, 1, 15, 9, tzinfo=UTC)
        assert visits[1].place_name == "work"
        assert visits[1].end is None

    def test_consecutive_same_state_collapsed(self):
        history = [
            make_state("home", 8),
            make_state("home", 8, 30),  # same zone, different timestamp
            make_state("work", 9),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert len(visits) == 2
        assert visits[0].place_name == "home"
        assert visits[1].place_name == "work"

    def test_not_home_state_has_unknown_source(self):
        history = [make_state("not_home", 10, lat=37.8, lng=-122.5)]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert len(visits) == 1
        assert visits[0].source == "unknown"
        assert visits[0].lat == 37.8
        assert visits[0].geocoded_location is None

    def test_geocoded_location_threaded_through(self):
        history = [
            make_state("not_home", 10, lat=37.8, lng=-122.5, geocoded_location="123 Main St")
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert visits[0].geocoded_location == "123 Main St"

    def test_address_state_sets_geocoded_location(self):
        # sensor.*_geocoded_location entity: state IS the address
        history = [make_state("123 Main St, City, CA 94110, USA", 8, lat=37.7, lng=-122.4)]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert visits[0].geocoded_location == "123 Main St, City, CA 94110, USA"

    def test_visit_id_is_deterministic(self):
        history = [make_state("home", 8)]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        v1 = derive_visits(history, ENTITY, window_end)
        v2 = derive_visits(history, ENTITY, window_end)

        assert v1[0].visit_id == v2[0].visit_id

    def test_visit_id_differs_for_different_start_times(self):
        h1 = [make_state("home", 8)]
        h2 = [make_state("home", 9)]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        v1 = derive_visits(h1, ENTITY, window_end)
        v2 = derive_visits(h2, ENTITY, window_end)

        assert v1[0].visit_id != v2[0].visit_id

    def test_short_completed_visit_filtered(self):
        # 5-min visit, threshold=10 → dropped
        history = [
            make_state("home", 8, 0),
            make_state("work", 8, 5),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end, min_visit_minutes=10)

        assert len(visits) == 1
        assert visits[0].place_name == "work"  # ongoing, 12h elapsed → kept

    def test_long_completed_visit_kept(self):
        history = [
            make_state("home", 8, 0),
            make_state("work", 8, 15),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end, min_visit_minutes=10)

        assert any(v.place_name == "home" for v in visits)

    def test_ongoing_visit_long_enough_kept(self):
        # ongoing visit started 15 min before window_end, threshold=10 → kept
        history = [make_state("home", 8, 45)]
        window_end = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end, min_visit_minutes=10)

        assert len(visits) == 1

    def test_ongoing_visit_too_short_filtered(self):
        # ongoing visit started 5 min before window_end, threshold=10 → dropped
        history = [make_state("home", 8, 55)]
        window_end = datetime(2024, 1, 15, 9, 0, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end, min_visit_minutes=10)

        assert visits == []

    def test_zero_threshold_keeps_all(self):
        history = [
            make_state("home", 8, 0),
            make_state("work", 8, 1),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end, min_visit_minutes=0)

        assert len(visits) == 2

    def test_full_day_sequence(self):
        history = [
            make_state("home", 6),
            make_state("not_home", 8, lat=37.8, lng=-122.5),
            make_state("work", 9),
            make_state("not_home", 12, lat=37.78, lng=-122.42),
            make_state("work", 13),
            make_state("home", 18),
        ]
        window_end = datetime(2024, 1, 15, 23, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)

        assert len(visits) == 6
        assert [v.place_name for v in visits] == [
            "home",
            "not_home",
            "work",
            "not_home",
            "work",
            "home",
        ]
        assert visits[-1].end is None  # last visit still ongoing


class TestMergeConsecutiveVisits:
    def _make_visit(self, place: str, hour_start: int, hour_end: int | None) -> object:
        from datetime import UTC, datetime

        from timeline_sync.visit_deriver import Visit

        return Visit(
            visit_id=f"id_{place}_{hour_start}",
            place_name=place,
            start=datetime(2024, 1, 15, hour_start, tzinfo=UTC),
            end=datetime(2024, 1, 15, hour_end, tzinfo=UTC) if hour_end else None,
            lat=0.0,
            lng=0.0,
            source="ha_zone",
        )

    def test_merges_adjacent_same_name(self):
        visits = [
            self._make_visit("Home", 8, 10),
            self._make_visit("Home", 10, 12),
        ]
        result = merge_consecutive_visits(visits)
        assert len(result) == 1
        assert result[0].place_name == "Home"
        assert result[0].start.hour == 8
        assert result[0].end is not None and result[0].end.hour == 12

    def test_keeps_visit_id_of_first(self):
        visits = [
            self._make_visit("Home", 8, 10),
            self._make_visit("Home", 10, 12),
        ]
        result = merge_consecutive_visits(visits)
        assert result[0].visit_id == "id_Home_8"

    def test_keeps_different_names_separate(self):
        visits = [
            self._make_visit("Home", 8, 9),
            self._make_visit("Work", 9, 17),
            self._make_visit("Home", 17, 20),
        ]
        result = merge_consecutive_visits(visits)
        assert len(result) == 3
        assert [v.place_name for v in result] == ["Home", "Work", "Home"]

    def test_merges_ongoing_end(self):
        visits = [
            self._make_visit("Home", 8, 10),
            self._make_visit("Home", 10, None),  # ongoing
        ]
        result = merge_consecutive_visits(visits)
        assert len(result) == 1
        assert result[0].end is None

    def test_empty_returns_empty(self):
        assert merge_consecutive_visits([]) == []


class TestGpsUpdateWithinSameState:
    def test_gps_updated_within_same_state_when_first_entry_has_no_coords(self):
        """GPS from a later same-state entry is used when the first had 0.0/0.0."""
        history = [
            make_state("123 Main St, City, CA", 10, 0, lat=0.0, lng=0.0),
            make_state("123 Main St, City, CA", 10, 1, lat=37.77, lng=-122.41),
            make_state("456 Oak Ave, City, CA", 11, 0),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)
        main_st = next(v for v in visits if "Main" in v.place_name)
        assert main_st.lat == 37.77
        assert main_st.lng == -122.41

    def test_geocoded_updated_within_same_state_when_first_entry_has_none(self):
        """geocoded_location from a later same-state entry is used when first had None."""
        history = [
            make_state("not_home", 10, 0, lat=37.77, lng=-122.41),
            make_state("not_home", 10, 5, lat=37.77, lng=-122.41, geocoded_location="500 Market St, SF"),
            make_state("home", 11, 0),
        ]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)
        not_home = next(v for v in visits if v.place_name == "not_home")
        assert not_home.geocoded_location == "500 Market St, SF"


class TestSensorLocationList:
    def test_sensor_location_list_parsed_as_lat_lng(self):
        # sensor.*_geocoded_location stores GPS as attrs["location"] = [lat, lng]
        history = [make_state("123 Main St, City, CA", 8, location=[37.7, -122.4])]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)
        assert visits[0].lat == 37.7
        assert visits[0].lng == -122.4

    def test_latitude_longitude_keys_still_work(self):
        history = [make_state("home", 8, lat=37.7, lng=-122.4)]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)
        assert visits[0].lat == 37.7
        assert visits[0].lng == -122.4

    def test_no_gps_attrs_gives_zero(self):
        history = [{"state": "home", "last_changed": dt(8), "attributes": {}}]
        window_end = datetime(2024, 1, 15, 20, tzinfo=UTC)
        visits = derive_visits(history, ENTITY, window_end)
        assert visits[0].lat == 0.0
        assert visits[0].lng == 0.0


class TestMergeNearbyVisits:
    def test_empty_returns_empty(self):
        assert merge_nearby_visits([]) == []

    def test_single_visit_unchanged(self):
        v = make_visit("Home", 8, 17, lat=37.7, lng=-122.4)
        assert merge_nearby_visits([v]) == [v]

    def test_two_visits_within_20m_merged(self):
        # 37.7, -122.4 and 37.70001, -122.4 are ~11 m apart
        v1 = make_visit("123 Oak St", 8, 9, lat=37.7, lng=-122.4)
        v2 = make_visit("125 Oak St", 9, 10, lat=37.70001, lng=-122.4)
        result = merge_nearby_visits([v1, v2])
        assert len(result) == 1
        assert result[0].start == v1.start
        assert result[0].end == v2.end

    def test_two_visits_far_apart_not_merged(self):
        # 1 km apart
        v1 = make_visit("Starbucks", 8, 9, lat=37.7, lng=-122.4)
        v2 = make_visit("Home Depot", 9, 10, lat=37.71, lng=-122.4)
        result = merge_nearby_visits([v1, v2])
        assert len(result) == 2

    def test_most_time_wins_for_place_name(self):
        # v1: 5 min at A, v2: 30 min at B (within 20m), v3: 5 min at C (within 20m of v2)
        # all within 20m of previous → one group; B has most time
        lat = 37.7
        lng = -122.4
        v1 = make_visit("Addr A", 8, None, lat=lat, lng=lng, minute_start=0)
        v1 = make_visit("Addr A", 8, 8, lat=lat, lng=lng, minute_start=0, minute_end=5)
        v2 = make_visit("Addr B", 8, 9, lat=lat + 0.00001, lng=lng, minute_start=5, minute_end=35)
        v3 = make_visit("Addr C", 9, 9, lat=lat + 0.00002, lng=lng, minute_start=35, minute_end=40)
        result = merge_nearby_visits([v1, v2, v3])
        assert len(result) == 1
        assert result[0].place_name == "Addr B"
        assert result[0].start == v1.start
        assert result[0].end == v3.end

    def test_no_gps_visits_not_merged(self):
        # lat=0, lng=0 → no GPS → don't merge even though coords "match"
        v1 = make_visit("Addr A", 8, 9, lat=0.0, lng=0.0)
        v2 = make_visit("Addr B", 9, 10, lat=0.0, lng=0.0)
        result = merge_nearby_visits([v1, v2])
        assert len(result) == 2

    def test_visit_id_of_merged_group_is_first_visits_id(self):
        v1 = make_visit("Addr A", 8, 9, lat=37.7, lng=-122.4)
        v2 = make_visit("Addr B", 9, 10, lat=37.70001, lng=-122.4)
        result = merge_nearby_visits([v1, v2])
        assert result[0].visit_id == v1.visit_id
