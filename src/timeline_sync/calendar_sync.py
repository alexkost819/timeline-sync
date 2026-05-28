from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .visit_deriver import Visit

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts.readonly",
]
VISIT_ID_KEY = "ha_visit_id"


def _event_body(visit: Visit, now: datetime) -> dict[str, Any]:
    end = visit.end or now
    description = f"lat: {visit.lat}, lng: {visit.lng}\nsource: {visit.source}"
    if visit.alternatives:
        description += f"\nOther options: {', '.join(visit.alternatives)}"
    body: dict[str, Any] = {
        "summary": visit.place_name,
        "description": description,
        "start": {"dateTime": visit.start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        "extendedProperties": {
            "private": {
                VISIT_ID_KEY: visit.visit_id,
                "ha_source": visit.source,
            }
        },
    }
    if visit.geocoded_location:
        body["location"] = visit.geocoded_location
    return body


class CalendarSync:
    def __init__(self, credentials: Credentials, calendar_name: str) -> None:
        self._service = build("calendar", "v3", credentials=credentials)
        self._calendar_name = calendar_name
        self._calendar_id: str | None = None

    def _get_or_create_calendar(self) -> str:
        if self._calendar_id:
            return self._calendar_id

        # Check if calendar already exists
        result = self._service.calendarList().list().execute()
        for item in result.get("items", []):
            if item.get("summary") == self._calendar_name:
                self._calendar_id = item["id"]
                return self._calendar_id

        # Create it
        calendar = self._service.calendars().insert(body={"summary": self._calendar_name}).execute()
        self._calendar_id = calendar["id"]
        log.info("Created calendar %r (%s)", self._calendar_name, self._calendar_id)
        return self._calendar_id

    def fetch_events(self, start: datetime, end: datetime) -> dict[str, dict]:
        """Return {visit_id: event} for all Timeline events in window."""
        calendar_id = self._get_or_create_calendar()
        return self._fetch_events_in_window(calendar_id, start, end)

    def _fetch_events_in_window(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> dict[str, dict]:
        events: dict[str, dict] = {}
        page_token = None
        while True:
            result = (
                self._service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=start.astimezone(UTC).isoformat(),
                    timeMax=end.astimezone(UTC).isoformat(),
                    privateExtendedProperty=f"{VISIT_ID_KEY}=*",
                    singleEvents=True,
                    pageToken=page_token,
                )
                .execute()
            )
            for event in result.get("items", []):
                vid = event.get("extendedProperties", {}).get("private", {}).get(VISIT_ID_KEY)
                if vid:
                    events[vid] = event
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return events

    def sync(
        self,
        visits: list[Visit],
        window_start: datetime,
        window_end: datetime,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        Diff visits against Calendar events in window, apply create/update/delete.
        Returns counts of operations performed.
        """
        now = datetime.now(UTC)
        calendar_id = self._get_or_create_calendar()
        existing = self._fetch_events_in_window(calendar_id, window_start, window_end)

        visit_map = {v.visit_id: v for v in visits}
        counts = {"created": 0, "updated": 0, "deleted": 0}

        # Create or update
        for visit_id, visit in visit_map.items():
            body = _event_body(visit, now)
            if visit_id in existing:
                existing_event = existing[visit_id]
                new_end = body["end"]["dateTime"]
                old_end = existing_event.get("end", {}).get("dateTime", "")
                new_summary = body["summary"]
                old_summary = existing_event.get("summary", "")
                new_loc = body.get("location", "")
                old_loc = existing_event.get("location", "")
                end_changed = new_end != old_end and visit.end is not None
                if new_summary != old_summary or end_changed or new_loc != old_loc:
                    log.info("Updating event for visit %s (%s)", visit_id, visit.place_name)
                    if not dry_run:
                        self._service.events().update(
                            calendarId=calendar_id,
                            eventId=existing_event["id"],
                            body=body,
                        ).execute()
                    counts["updated"] += 1
            else:
                log.info("Creating event for visit %s (%s)", visit_id, visit.place_name)
                if not dry_run:
                    self._service.events().insert(calendarId=calendar_id, body=body).execute()
                counts["created"] += 1

        # Delete events no longer in HA data
        for visit_id, event in existing.items():
            if visit_id not in visit_map:
                log.info("Deleting event for visit %s", visit_id)
                if not dry_run:
                    try:
                        self._service.events().delete(
                            calendarId=calendar_id, eventId=event["id"]
                        ).execute()
                    except HttpError as e:
                        if e.resp.status != 410:  # 410 = already deleted
                            raise
                counts["deleted"] += 1

        return counts
