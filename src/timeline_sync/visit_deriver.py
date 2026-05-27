from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class Visit:
    visit_id: str
    place_name: str
    start: datetime
    end: datetime | None  # None = ongoing
    lat: float
    lng: float
    source: Literal["ha_zone", "places_api", "geocode", "unknown"]
    geocoded_location: str | None = field(default=None)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _visit_id(entity_id: str, place_name: str, start: datetime) -> str:
    key = f"{entity_id}|{place_name}|{start.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def derive_visits(
    state_history: list[dict[str, Any]],
    entity_id: str,
    window_end: datetime,
) -> list[Visit]:
    """
    Convert raw HA state history into Visit objects.

    Consecutive states with the same value are collapsed into one visit.
    geocoded_location is captured from HA companion app attributes when present.
    """
    if not state_history:
        return []

    visits: list[Visit] = []
    current_state: str | None = None
    current_start: datetime | None = None
    current_lat: float = 0.0
    current_lng: float = 0.0
    current_geocoded: str | None = None

    for entry in state_history:
        state = entry.get("state", "")
        changed_at = _parse_dt(entry["last_changed"])
        attrs = entry.get("attributes", {})
        lat = float(attrs.get("latitude", 0.0))
        lng = float(attrs.get("longitude", 0.0))
        geocoded = attrs.get("geocoded_location") or None

        if state != current_state:
            if current_state is not None and current_start is not None:
                source: Literal["ha_zone", "places_api", "geocode", "unknown"] = (
                    "ha_zone" if current_state != "not_home" else "unknown"
                )
                visits.append(
                    Visit(
                        visit_id=_visit_id(entity_id, current_state, current_start),
                        place_name=current_state,
                        start=current_start,
                        end=changed_at,
                        lat=current_lat,
                        lng=current_lng,
                        source=source,
                        geocoded_location=current_geocoded,
                    )
                )
            current_state = state
            current_start = changed_at
            current_lat = lat
            current_lng = lng
            current_geocoded = geocoded

    if current_state is not None and current_start is not None:
        source = "ha_zone" if current_state != "not_home" else "unknown"
        visits.append(
            Visit(
                visit_id=_visit_id(entity_id, current_state, current_start),
                place_name=current_state,
                start=current_start,
                end=None,
                lat=current_lat,
                lng=current_lng,
                source=source,
                geocoded_location=current_geocoded,
            )
        )

    return visits
