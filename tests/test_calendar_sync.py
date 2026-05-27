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
                "summary": "@ Home",
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
            "summary": "@ Home",
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
                "summary": "@ Home",
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
                "summary": "@ Home",
                "start": {"dateTime": "2024-01-15T08:00:00+00:00"},
                "end": {"dateTime": "2024-01-15T17:00:00+00:00"},
                "extendedProperties": {"private": {"ha_visit_id": "abc123"}},
            }
        ]
        syncer = self._make_syncer(existing)
        counts = syncer.sync([visit], WINDOW_START, WINDOW_END, dry_run=True)
        assert counts == {"created": 0, "updated": 0, "deleted": 0}
