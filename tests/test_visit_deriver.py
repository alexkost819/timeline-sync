from datetime import UTC, datetime

from timeline_sync.visit_deriver import derive_visits

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
) -> dict:
    attrs: dict = {"latitude": lat, "longitude": lng}
    if geocoded_location:
        attrs["geocoded_location"] = geocoded_location
    return {"state": state, "last_changed": dt(hour, minute), "attributes": attrs}


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
