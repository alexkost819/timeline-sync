from __future__ import annotations

import dataclasses
import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal


@dataclass(frozen=True)
class Visit:
    visit_id: str
    place_name: str
    start: datetime
    end: datetime | None  # None = ongoing
    lat: float
    lng: float
    source: Literal["ha_zone", "places_api", "geocode", "contact", "unknown"]
    geocoded_location: str | None = field(default=None)
    alternatives: tuple[str, ...] = field(default=())


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _visit_id(entity_id: str, place_name: str, start: datetime) -> str:
    key = f"{entity_id}|{place_name}|{start.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def derive_visits(
    state_history: list[dict[str, Any]],
    entity_id: str,
    window_end: datetime,
    min_visit_minutes: int = 0,
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
        loc = attrs.get("location") or []
        lat = float(attrs.get("latitude") or (loc[0] if len(loc) >= 2 else 0.0))
        lng = float(attrs.get("longitude") or (loc[1] if len(loc) >= 2 else 0.0))
        geocoded = attrs.get("geocoded_location") or None
        # sensor.*_geocoded_location entity: state IS the address
        if geocoded is None and ", " in state:
            geocoded = state

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

    if min_visit_minutes > 0:
        threshold = timedelta(minutes=min_visit_minutes)
        visits = [
            v for v in visits if (v.end if v.end is not None else window_end) - v.start >= threshold
        ]

    return visits


def merge_consecutive_visits(visits: list[Visit]) -> list[Visit]:
    """Merge adjacent visits with the same place_name into one spanning visit."""
    if not visits:
        return []
    merged = [visits[0]]
    for v in visits[1:]:
        prev = merged[-1]
        if v.place_name == prev.place_name:
            merged[-1] = replace(prev, end=v.end)
        else:
            merged.append(v)
    return merged


_EARTH_RADIUS_M = 6_371_000


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return 2 * math.asin(math.sqrt(a)) * _EARTH_RADIUS_M


def _best_of_group(group: list[Visit]) -> Visit:
    if len(group) == 1:
        return group[0]
    window_end = group[-1].end or group[-1].start
    best = max(group, key=lambda v: ((v.end or window_end) - v.start).total_seconds())
    return replace(best, start=group[0].start, end=group[-1].end, visit_id=group[0].visit_id)


def merge_nearby_visits(visits: list[Visit], radius_m: float = 20.0) -> list[Visit]:
    """Merge consecutive visits whose GPS coords are within radius_m. Most-time visit provides name."""
    if not visits:
        return []
    result: list[Visit] = []
    group: list[Visit] = [visits[0]]
    for v in visits[1:]:
        prev = group[-1]
        if (prev.lat or prev.lng) and _haversine_m(prev.lat, prev.lng, v.lat, v.lng) <= radius_m:
            group.append(v)
        else:
            result.append(_best_of_group(group))
            group = [v]
    result.append(_best_of_group(group))
    return result
