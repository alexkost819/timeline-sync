from __future__ import annotations

import hashlib
from dataclasses import dataclass
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


def _parse_dt(value: str) -> datetime:
    """Parse HA last_changed/last_updated ISO string to aware datetime."""
    # HA returns strings like "2024-01-15T08:30:00.123456+00:00"
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
    Convert raw HA state history into a list of Visit objects.

    Each Visit represents a contiguous block of time where the device was in
    the same state (zone name or "not_home"). Consecutive states with the same
    value are collapsed into one visit.
    """
    if not state_history:
        return []

    visits: list[Visit] = []
    current_state: str | None = None
    current_start: datetime | None = None
    current_lat: float = 0.0
    current_lng: float = 0.0

    for entry in state_history:
        state = entry.get("state", "")
        changed_at = _parse_dt(entry["last_changed"])
        attrs = entry.get("attributes", {})
        lat = float(attrs.get("latitude", 0.0))
        lng = float(attrs.get("longitude", 0.0))

        if state != current_state:
            # Close previous visit
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
                    )
                )
            current_state = state
            current_start = changed_at
            current_lat = lat
            current_lng = lng

    # Close the final (possibly ongoing) visit
    if current_state is not None and current_start is not None:
        source = "ha_zone" if current_state != "not_home" else "unknown"
        visits.append(
            Visit(
                visit_id=_visit_id(entity_id, current_state, current_start),
                place_name=current_state,
                start=current_start,
                end=None,  # ongoing
                lat=current_lat,
                lng=current_lng,
                source=source,
            )
        )

    return visits
