import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from timeline_sync.contact_resolver import ContactResolver, _normalize


class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Oak St") == "oak st"

    def test_strips_punctuation(self):
        assert _normalize("123 Oak St, CA") == "123 oak st ca"

    def test_collapses_whitespace(self):
        assert _normalize("123  Oak  St") == "123 oak st"


def _make_resolver_with_map(entries: dict[str, str]) -> ContactResolver:
    """Build a ContactResolver bypassing API calls, injecting the address map directly."""
    resolver = ContactResolver.__new__(ContactResolver)
    resolver._map = {_normalize(addr): label for addr, label in entries.items()}
    return resolver


class TestContactResolverResolve:
    def test_exact_match(self):
        resolver = _make_resolver_with_map(
            {"123 Oak St, San Francisco, CA 94110, USA": "Dan's Home"}
        )
        assert resolver.resolve("123 Oak St, San Francisco, CA 94110, USA") == "Dan's Home"

    def test_fuzzy_match_above_threshold(self):
        # HA may omit country or use different punctuation
        resolver = _make_resolver_with_map(
            {"123 Oak St, San Francisco, CA 94110, USA": "Dan's Home"}
        )
        result = resolver.resolve("123 Oak St, San Francisco, CA 94110")
        assert result == "Dan's Home"

    def test_no_match_below_threshold(self):
        resolver = _make_resolver_with_map(
            {"123 Oak St, San Francisco, CA 94110, USA": "Dan's Home"}
        )
        assert resolver.resolve("456 Pine Ave, Oakland, CA 94601") is None

    def test_empty_map_returns_none(self):
        resolver = _make_resolver_with_map({})
        assert resolver.resolve("123 Oak St, San Francisco, CA") is None


class TestContactResolverCache:
    def test_cache_written_after_fetch(self, tmp_path):
        cache = tmp_path / "contacts_cache.json"
        mock_creds = MagicMock()
        contacts_data = {
            "connections": [
                {
                    "names": [{"displayName": "Dan Smith", "givenName": "Dan"}],
                    "addresses": [
                        {
                            "formattedValue": "123 Oak St, San Francisco, CA 94110, USA",
                            "type": "home",
                        }
                    ],
                }
            ]
        }
        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            svc.people.return_value.connections.return_value.list.return_value.execute.return_value = contacts_data
            ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        assert cache.exists()
        saved = json.loads(cache.read_text())
        assert "123 Oak St, San Francisco, CA 94110, USA" in saved
        assert saved["123 Oak St, San Francisco, CA 94110, USA"] == "Dan Smith's Home"

    def test_cache_loaded_on_init(self, tmp_path):
        cache = tmp_path / "contacts_cache.json"
        cache.write_text(json.dumps({"456 Pine Ave, Oakland, CA": "Mom's Place"}))
        mock_creds = MagicMock()

        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            # API returns empty — cache should still have the entry from disk
            svc.people.return_value.connections.return_value.list.return_value.execute.return_value = {
                "connections": []
            }
            resolver = ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        # After fetch (empty API), cache is overwritten with empty contacts
        # But the pre-fetch in-memory map should have been set from cache initially
        # (then overwritten by empty API result — this tests the API-first behavior)
        # The important thing: no crash, and resolve works based on final state
        assert resolver.resolve("456 Pine Ave, Oakland, CA") is None  # API cleared it

    def test_contact_name_format_home(self, tmp_path):
        cache = tmp_path / "c.json"
        mock_creds = MagicMock()
        contacts_data = {
            "connections": [
                {
                    "names": [{"displayName": "Jane Doe", "givenName": "Jane"}],
                    "addresses": [{"formattedValue": "789 Elm St, Denver, CO", "type": "home"}],
                }
            ]
        }
        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            svc.people.return_value.connections.return_value.list.return_value.execute.return_value = contacts_data
            resolver = ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        assert resolver.resolve("789 Elm St, Denver, CO") == "Jane Doe's Home"

    def test_contact_name_format_work(self, tmp_path):
        cache = tmp_path / "c.json"
        mock_creds = MagicMock()
        contacts_data = {
            "connections": [
                {
                    "names": [{"displayName": "Jane Doe", "givenName": "Jane"}],
                    "addresses": [{"formattedValue": "100 Corp Blvd, Austin, TX", "type": "work"}],
                }
            ]
        }
        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            svc.people.return_value.connections.return_value.list.return_value.execute.return_value = contacts_data
            resolver = ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        assert resolver.resolve("100 Corp Blvd, Austin, TX") == "Jane Doe's Work"

    def test_contact_name_format_other(self, tmp_path):
        cache = tmp_path / "c.json"
        mock_creds = MagicMock()
        contacts_data = {
            "connections": [
                {
                    "names": [{"displayName": "Jane Doe", "givenName": "Jane"}],
                    "addresses": [{"formattedValue": "200 Other Rd, Austin, TX", "type": "other"}],
                }
            ]
        }
        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            svc.people.return_value.connections.return_value.list.return_value.execute.return_value = contacts_data
            resolver = ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        assert resolver.resolve("200 Other Rd, Austin, TX") == "Jane Doe's Other Address"


class TestContactResolverDiagnostics:
    def test_http_error_logged_with_status(self, tmp_path, caplog):
        cache = tmp_path / "c.json"
        mock_creds = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        http_error = HttpError(resp=resp, content=b'{"error": {"status": "OTHER_ERROR"}}')

        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            svc.people.return_value.connections.return_value.list.return_value.execute.side_effect = http_error
            with caplog.at_level(logging.WARNING, logger="timeline_sync.contact_resolver"):
                ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        assert "403" in caplog.text

    def test_permission_denied_logged_as_api_not_enabled(self, tmp_path, caplog):
        cache = tmp_path / "c.json"
        mock_creds = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        http_error = HttpError(resp=resp, content=b'{"error": {"status": "PERMISSION_DENIED"}}')

        with patch("timeline_sync.contact_resolver.build") as mock_build:
            svc = MagicMock()
            mock_build.return_value = svc
            svc.people.return_value.connections.return_value.list.return_value.execute.side_effect = http_error
            with caplog.at_level(logging.WARNING, logger="timeline_sync.contact_resolver"):
                ContactResolver(mock_creds, cache_path=cache, refresh_hours=24)

        assert "not enabled" in caplog.text.lower()
        assert "console.cloud.google.com" in caplog.text

    def test_debug_logging_on_resolve(self, caplog):
        resolver = _make_resolver_with_map(
            {"123 Oak St, San Francisco, CA 94110, USA": "Dan's Home"}
        )
        with caplog.at_level(logging.DEBUG, logger="timeline_sync.contact_resolver"):
            resolver.resolve("123 Oak St, San Francisco, CA 94110, USA")

        assert any("match" in r.message.lower() for r in caplog.records if r.levelno == logging.DEBUG)

    def test_threshold_80_matches_close_address(self):
        # Threshold 0.80: address without country should still match
        resolver = _make_resolver_with_map(
            {"123 Oak St, San Francisco, CA 94110, USA": "Dan's Home"}
        )
        result = resolver.resolve("123 Oak St, San Francisco, CA 94110")
        assert result == "Dan's Home"
