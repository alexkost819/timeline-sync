from __future__ import annotations

import asyncio
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)


def _normalize(address: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy comparison."""
    address = address.lower()
    address = re.sub(r"[^\w\s]", " ", address)
    return re.sub(r"\s+", " ", address).strip()


class ContactResolver:
    """
    Resolves geocoded addresses to personal contact names via Google People API.

    Contacts are fetched at startup, cached to disk, and refreshed in the background
    every refresh_hours hours.
    """

    MATCH_THRESHOLD = 0.80

    def __init__(
        self,
        credentials: Credentials,
        cache_path: Path | None = None,
        refresh_hours: int = 24,
    ) -> None:
        self._credentials = credentials
        self._cache_path = cache_path
        self._refresh_hours = refresh_hours
        # normalized_address -> display label
        self._map: dict[str, str] = {}

        if cache_path and cache_path.exists():
            self._load_cache(cache_path)

        try:
            self._fetch_and_update()
        except HttpError as e:
            if e.resp.status == 403 and b"PERMISSION_DENIED" in (e.content or b""):
                log.warning(
                    "People API not enabled in this Google Cloud project — "
                    "enable it at console.cloud.google.com/apis/library/people.googleapis.com"
                )
            else:
                log.warning(
                    "People API returned %s — check contacts.readonly OAuth scope (re-auth required)",
                    e.resp.status,
                )
        except Exception:
            log.warning("Failed to fetch contacts from People API; using cache if available")

    def resolve(self, address: str) -> str | None:
        """Return contact label if address fuzzy-matches a contact, else None."""
        norm = _normalize(address)
        best_ratio = 0.0
        best_name: str | None = None
        for contact_norm, name in self._map.items():
            ratio = SequenceMatcher(None, norm, contact_norm).ratio()
            log.debug("contact match %.2f: %r → %r", ratio, norm, name)
            if ratio > best_ratio:
                best_ratio = ratio
                best_name = name
        if best_ratio >= self.MATCH_THRESHOLD:
            return best_name
        return None

    async def watch(self) -> None:
        """Background coroutine: re-fetch contacts every refresh_hours hours."""
        while True:
            await asyncio.sleep(self._refresh_hours * 3600)
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._fetch_and_update)
                log.info("Contacts refreshed: %d addresses loaded", len(self._map))
            except Exception:
                log.warning("Failed to refresh contacts from People API")

    def _fetch_and_update(self) -> None:
        raw = self._fetch_contacts()
        self._map = {_normalize(addr): label for addr, label in raw.items()}
        log.info("Loaded %d contact addresses", len(self._map))
        if self._cache_path:
            self._save_cache(raw, self._cache_path)

    def _fetch_contacts(self) -> dict[str, str]:
        """Fetch all contacts with addresses from People API. Returns {address: label}."""
        service = build("people", "v1", credentials=self._credentials)
        contacts: dict[str, str] = {}
        page_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "resourceName": "people/me",
                "personFields": "names,addresses",
                "pageSize": 1000,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            result = service.people().connections().list(**kwargs).execute()
            for person in result.get("connections", []):
                label = self._person_label(person)
                if not label:
                    continue
                for addr in person.get("addresses", []):
                    formatted = addr.get("formattedValue", "").strip()
                    if not formatted:
                        continue
                    addr_type = addr.get("type", "").lower()
                    if addr_type == "home":
                        display = f"{label}'s Home" if label else "Home"
                    elif addr_type == "work":
                        display = f"{label}'s Work" if label else "Work"
                    else:
                        display = f"{label}'s Other Address" if label else "Other Address"
                    contacts[formatted] = display
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return contacts

    def _given_name(self, person: dict[str, Any]) -> str:
        names = person.get("names", [])
        if not names:
            return ""
        return names[0].get("givenName", "")

    def _person_label(self, person: dict[str, Any]) -> str:
        names = person.get("names", [])
        if not names:
            return ""
        return names[0].get("displayName", names[0].get("givenName", ""))

    def _load_cache(self, path: Path) -> None:
        try:
            with open(path) as f:
                raw: dict[str, str] = json.load(f)
            self._map = {_normalize(addr): label for addr, label in raw.items()}
            log.debug("Loaded %d contact addresses from cache", len(self._map))
        except Exception:
            log.warning("Could not load contacts cache from %s", path)

    def _save_cache(self, contacts: dict[str, str], path: Path) -> None:
        try:
            with open(path, "w") as f:
                json.dump(contacts, f, indent=2)
        except Exception:
            log.warning("Could not save contacts cache to %s", path)
