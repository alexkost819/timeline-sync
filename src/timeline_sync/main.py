from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pickle
from datetime import UTC, datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .calendar_sync import SCOPES, CalendarSync
from .config import load_config
from .ha_reader import HAReader
from .place_resolver import PlaceResolver
from .quota import DailyQuota
from .visit_deriver import derive_visits

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


async def run_sync(cfg, dry_run: bool = False) -> None:
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

    known_names: dict[str, str] = {}
    creds = None
    syncer = None
    if not dry_run:
        creds = _get_credentials(cfg.google_credentials_file)
        syncer = CalendarSync(creds, cfg.google_calendar_name)
        existing = syncer.fetch_events(window_start, now)
        known_names = {
            vid: event["summary"]
            for vid, event in existing.items()
            if event.get("extendedProperties", {}).get("private", {}).get("ha_source")
            in ("places_api", "geocode")
        }

    quota = DailyQuota(limit=cfg.places_daily_limit) if cfg.places_api_key else None
    resolver = PlaceResolver(zones, cfg.places_api_key, quota=quota, known_names=known_names)
    enriched = await asyncio.gather(*[resolver.enrich_visit(v) for v in visits])

    if dry_run:
        log.info("DRY RUN — %d visits derived:", len(enriched))
        for v in enriched:
            end_str = v.end.isoformat() if v.end else "ongoing"
            log.info("  [%s] %s → %s  (%s)", v.source, v.start.isoformat(), end_str, v.place_name)
        return

    assert syncer is not None
    counts = syncer.sync(list(enriched), window_start, now, dry_run=False)
    log.info("Sync complete: %s", counts)


async def listen_and_sync(cfg) -> None:
    """Run an initial sync, then trigger on every HA location state change."""
    log.info("Running initial sync on startup.")
    await run_sync(cfg)

    reader = HAReader(cfg.ha_url, cfg.ha_token)
    await reader.listen_state_changes(
        cfg.ha_entity,
        lambda: run_sync(cfg),
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
