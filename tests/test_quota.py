import json
from datetime import date
from pathlib import Path

import pytest

from timeline_sync.quota import DailyQuota

DAY1 = date(2024, 1, 14)
DAY2 = date(2024, 1, 15)


@pytest.fixture
def tmp_quota(tmp_path: Path) -> DailyQuota:
    return DailyQuota(limit=5, path=tmp_path / "quota.json")


class TestDailyQuota:
    def test_consume_within_limit(self, tmp_quota: DailyQuota):
        for _ in range(5):
            assert tmp_quota.consume(DAY1) is True

    def test_consume_exceeds_limit(self, tmp_quota: DailyQuota):
        for _ in range(5):
            tmp_quota.consume(DAY1)
        assert tmp_quota.consume(DAY1) is False

    def test_remaining_decrements(self, tmp_quota: DailyQuota):
        assert tmp_quota.remaining(DAY1) == 5
        tmp_quota.consume(DAY1)
        assert tmp_quota.remaining(DAY1) == 4

    def test_each_date_has_independent_budget(self, tmp_quota: DailyQuota):
        for _ in range(5):
            tmp_quota.consume(DAY1)
        # DAY1 exhausted, DAY2 still has full budget
        assert tmp_quota.consume(DAY1) is False
        assert tmp_quota.consume(DAY2) is True
        assert tmp_quota.remaining(DAY2) == 4

    def test_persists_across_instances(self, tmp_path: Path):
        path = tmp_path / "quota.json"
        q1 = DailyQuota(limit=5, path=path)
        q1.consume(DAY1)
        q1.consume(DAY1)

        q2 = DailyQuota(limit=5, path=path)
        assert q2.used(DAY1) == 2
        assert q2.remaining(DAY1) == 3

    def test_old_dates_never_reset(self, tmp_path: Path):
        path = tmp_path / "quota.json"
        path.write_text(json.dumps({"2024-01-10": 5, "2024-01-11": 3}))

        q = DailyQuota(limit=5, path=path)
        assert q.used(date(2024, 1, 10)) == 5
        assert q.used(date(2024, 1, 11)) == 3
        assert q.consume(date(2024, 1, 10)) is False
        assert q.consume(date(2024, 1, 11)) is True

    def test_used_reflects_count(self, tmp_quota: DailyQuota):
        tmp_quota.consume(DAY1)
        tmp_quota.consume(DAY1)
        assert tmp_quota.used(DAY1) == 2
        assert tmp_quota.used(DAY2) == 0
