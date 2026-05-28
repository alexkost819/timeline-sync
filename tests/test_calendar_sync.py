from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from timeline_sync.calendar_sync import CalendarSync
from timeline_sync.visit_deriver import Visit

WINDOW_START = datetime(2024, 1, 15, 0, tzinfo=UTC)
WINDOW_END = datetime(2024, 1, 15, 23, 59, tzinfo=UTC)


def make_visit(visit_id: str, place_name: str, hour_start: int, hour_end: int | None) -> Visit:
    return Visit(
        visit_id=visit_id,
        place_name=place_name,
        start=datetime(2024, 1, 15, hour_start, tzinfo=UTC),
        end=datetime(2024, 1, 15, hour_end, tzinfo=UTC) if hour_end else None,
        lat=37.7,
        lng=-122.4,
        source="ha_zone",
    )


def make_mock_service(existing_events: list[dict]) -> MagicMock:
    service = MagicMock()

    # Calendar list — return empty so it creates a new calendar
    service.calendarList().list().execute.return_value = {"items": []}
    service.calendars().insert().execute.return_value = {"id": "cal123"}

    # Events list — return existing_events
    service.events().list().execute.return_value = {
        "items": existing_events,
        "nextPageToken": None,
    }

    return service


class TestCalendarSyncDiff:
    def _make_syncer(self, existing_events: list[dict]) -> CalendarSync:
        mock_creds = MagicMock()
        with patch(
            "timeline_sync.calendar_sync.build", return_value=make_mock_service(existing_events)
        ):
            syncer = CalendarSync(mock_creds, "Timeline")
        return syncer

    def test_new_visit_creates_event(self):
        syncer = self._make_syncer([])
        visits = [make_visit("abc123", "Home", 8, 17)]
        counts = syncer.sync(visits, WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["created"] == 1
        assert counts["updated"] == 0
        assert counts["deleted"] == 0

    def test_unchanged_visit_not_updated(self):
        visit = make_visit("abc123", "Home", 8, 17)
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T17:00:00+00:00"},
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["updated"] == 0

    def test_renamed_visit_triggers_delete_and_create(self):
        # visit_id changes when place_name changes → old deleted, new created
        old_event = {
            "id": "evt_old",
            "summary": "Home",
            "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
            "end": {"dateTime": "2024-01-15T09:00:00+00:00"},
            "extendedProperties": {"private": {"ha_visit_id": "old_id"}},
        }
        new_visit = make_visit("new_id", "Casa", 8, 9)
        syncer = self._make_syncer([old_event])
        counts = syncer.sync([new_visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["created"] == 1
        assert counts["deleted"] == 1

    def test_removed_visit_deleted_from_calendar(self):
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T17:00:00+00:00"},
                "extendedProperties": {"private": {"ha_visit_id": "gone123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["deleted"] == 1

    def test_idempotent_double_run(self):
        visit = make_visit("abc123", "Home", 8, 17)
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T17:00:00+00:00"},
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts == {"created": 0, "updated": 0, "deleted": 0, "unchanged": 1}

    def test_location_field_set_when_geocoded(self):
        from timeline_sync.calendar_sync import _event_body
        from datetime import UTC, datetime

        visit = Visit(
            visit_id="abc",
            place_name="Dan's House",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 10, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="contact",
            geocoded_location="123 Oak St, San Francisco, CA 94110, USA",
        )
        body = _event_body(visit, datetime(2024, 1, 15, 9, tzinfo=UTC))
        assert body["location"] == "123 Oak St, San Francisco, CA 94110, USA"

    def test_location_field_absent_when_no_geocoded(self):
        from timeline_sync.calendar_sync import _event_body
        from datetime import UTC, datetime

        visit = Visit(
            visit_id="abc",
            place_name="Home",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 10, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="ha_zone",
        )
        body = _event_body(visit, datetime(2024, 1, 15, 9, tzinfo=UTC))
        assert "location" not in body

    def test_no_description_when_no_alternatives(self):
        from timeline_sync.calendar_sync import _event_body
        from datetime import UTC, datetime

        visit = Visit(
            visit_id="abc",
            place_name="Home",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 10, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="ha_zone",
        )
        body = _event_body(visit, datetime(2024, 1, 15, 9, tzinfo=UTC))
        assert "description" not in body or not body["description"]

    def test_alternatives_in_description(self):
        from timeline_sync.calendar_sync import _event_body
        from datetime import UTC, datetime

        visit = Visit(
            visit_id="abc",
            place_name="Starbucks",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 9, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="places_api",
            alternatives=("Peet's Coffee", "Blue Bottle"),
        )
        body = _event_body(visit, datetime(2024, 1, 15, 9, tzinfo=UTC))
        assert "Peet's Coffee" in body["description"]
        assert "Blue Bottle" in body["description"]

    def test_fetch_events_does_not_use_extended_property_wildcard(self):
        syncer = self._make_syncer([])
        syncer.fetch_events(WINDOW_START, WINDOW_END)
        list_kwargs = syncer._service.events.return_value.list.call_args.kwargs
        assert list_kwargs.get("privateExtendedProperty") != "ha_visit_id=*"

    def test_ongoing_visit_not_updated_on_end_time_only(self):
        # visit.end is None → ongoing; only end time differs → skip update
        visit = Visit(
            visit_id="abc123",
            place_name="Home",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=None,  # ongoing
            lat=37.7,
            lng=-122.4,
            source="ha_zone",
        )
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T12:00:00+00:00"},  # stale end
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["updated"] == 0

    def test_completed_visit_updated_on_end_time_change(self):
        # visit.end is set → completed; end time differs → trigger update
        visit = Visit(
            visit_id="abc123",
            place_name="Home",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 17, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="ha_zone",
        )
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T16:00:00+00:00"},  # old end
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["updated"] == 1

    def test_z_suffix_datetime_not_spuriously_updated(self):
        # Google Calendar returns "Z" suffix; our code produces "+00:00" — must be treated equal
        visit = Visit(
            visit_id="abc123",
            place_name="Home",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 17, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="ha_zone",
        )
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00Z"},   # Google returns Z
                "end": {"dateTime": "2024-01-15T17:00:00Z"},     # same time, Z format
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["updated"] == 0
        assert counts.get("unchanged", 0) + counts.get("updated", 0) == 1  # event was seen

    def test_microsecond_end_time_not_spuriously_updated(self):
        # HA timestamps have microseconds; Google Calendar truncates to seconds on storage.
        # Second run must treat 17:00:00.567782 == 17:00:00 as equal.
        visit = Visit(
            visit_id="abc123",
            place_name="Home",
            start=datetime(2024, 1, 15, 8, tzinfo=UTC),
            end=datetime(2024, 1, 15, 17, 0, 0, 567782, tzinfo=UTC),
            lat=37.7,
            lng=-122.4,
            source="ha_zone",
        )
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T17:00:00+00:00"},
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["updated"] == 0

    def test_unchanged_count_incremented_for_no_change(self):
        visit = make_visit("abc123", "Home", 8, 17)
        existing = [
            {
                "id": "evt1",
                "summary": "Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T17:00:00+00:00"},
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts["unchanged"] == 1
        assert counts["updated"] == 0
