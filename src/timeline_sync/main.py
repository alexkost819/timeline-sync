from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .calendar_sync import SCOPES, CalendarSync
from .config import load_config
from .contact_resolver import ContactResolver
from .ha_reader import HAReader
from .place_resolver import PlaceResolver
from .quota import DailyQuota
from .visit_deriver import derive_visits, merge_consecutive_visits, merge_nearby_visits

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

TOKEN_FILE = "token.pickle"


def _get_credentials(credentials_file: str) -> Credentials:
    creds: Credentials | None = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return creds


async def run_sync(
    cfg,
    dry_run: bool = False,
    contact_resolver: ContactResolver | None = None,
) -> None:
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=cfg.sync_window_hours)

    reader = HAReader(cfg.ha_url, cfg.ha_token)
    state_history, zones = await asyncio.gather(
        reader.get_state_history(cfg.ha_entity, window_start, now),
        reader.get_zones(),
    )

    visits = derive_visits(
        state_history, cfg.ha_entity, now, min_visit_minutes=cfg.min_visit_minutes
    )
    visits = merge_nearby_visits(visits, radius_m=cfg.gps_proximity_meters)

    known_names: dict[str, str] = {}
    creds = None
    syncer = None
    cr = contact_resolver
    if not dry_run:
        creds = _get_credentials(cfg.google_credentials_file)
        syncer = CalendarSync(creds, cfg.google_calendar_name)
        existing = syncer.fetch_events(window_start, now)
        known_names = {
            vid: event["summary"]
            for vid, event in existing.items()
            if event.get("extendedProperties", {}).get("private", {}).get("ha_source")
            in ("places_api", "geocode", "contact")
        }
        if cr is None:
            cr = ContactResolver(
                creds,
                cache_path=Path(cfg.contacts_cache_file),
                refresh_hours=cfg.contacts_refresh_hours,
            )

    quota = DailyQuota(limit=cfg.places_daily_limit) if cfg.places_api_key else None
    resolver = PlaceResolver(
        zones,
        cfg.places_api_key,
        quota=quota,
        known_names=known_names,
        contact_resolver=cr,
    )
    enriched = await asyncio.gather(*[resolver.enrich_visit(v) for v in visits])
    merged = merge_consecutive_visits(list(enriched))

    if dry_run:
        log.info("DRY RUN — %d visits derived (after merge):", len(merged))
        for v in merged:
            end_str = v.end.isoformat() if v.end else "ongoing"
            log.info("  [%s] %s → %s  (%s)", v.source, v.start.isoformat(), end_str, v.place_name)
            if v.alternatives:
                log.info("    Other options: %s", ", ".join(v.alternatives))
        return

    assert syncer is not None
    counts = syncer.sync(list(merged), window_start, now, dry_run=False)
    log.info("Sync complete: %s", counts)


async def listen_and_sync(cfg) -> None:
    """Run an initial sync, then trigger on every HA location state change."""
    creds = _get_credentials(cfg.google_credentials_file)
    contact_resolver = ContactResolver(
        creds,
        cache_path=Path(cfg.contacts_cache_file),
        refresh_hours=cfg.contacts_refresh_hours,
    )

    log.info("Running initial sync on startup.")
    await run_sync(cfg, contact_resolver=contact_resolver)

    reader = HAReader(cfg.ha_url, cfg.ha_token)

    async def sync_callback() -> None:
        await run_sync(cfg, contact_resolver=contact_resolver)

    await asyncio.gather(
        reader.listen_state_changes(cfg.ha_entity, sync_callback),
        contact_resolver.watch(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Home Assistant Timeline → Google Calendar")
    parser.add_argument("--dry-run", action="store_true", help="Print visits, skip Calendar writes")
    parser.add_argument("--once", action="store_true", help="Run sync once and exit")
    args = parser.parse_args()

    cfg = load_config()

    if args.dry_run or args.once:
        asyncio.run(run_sync(cfg, dry_run=args.dry_run))
    else:
        asyncio.run(listen_and_sync(cfg))


if __name__ == "__main__":
    main()
