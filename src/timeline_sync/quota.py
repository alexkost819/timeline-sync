from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".timeline-sync" / "quota.json"
DEFAULT_DAILY_LIMIT = 300


class DailyQuota:
    """
    Tracks Places API calls per visited date (not per script-run date).
    A visit on 2026-05-25 counts against 2026-05-25's budget regardless of
    when the sync runs. Persisted to a JSON file keyed by ISO date string.
    """

    def __init__(self, limit: int = DEFAULT_DAILY_LIMIT, path: Path = DEFAULT_PATH) -> None:
        self._limit = limit
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                if isinstance(data, dict):
                    return {k: int(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                pass
        return {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data))

    def used(self, visit_date: date) -> int:
        return self._data.get(visit_date.isoformat(), 0)

    def remaining(self, visit_date: date) -> int:
        return max(0, self._limit - self.used(visit_date))

    def consume(self, visit_date: date) -> bool:
        """Consume one unit for visit_date. Returns True if allowed."""
        key = visit_date.isoformat()
        count = self._data.get(key, 0)
        if count >= self._limit:
            log.warning(
                "Places API quota for %s exhausted (%d/%d). Skipping enrichment.",
                key,
                count,
                self._limit,
            )
            return False
        self._data[key] = count + 1
        self._save()
        return True
