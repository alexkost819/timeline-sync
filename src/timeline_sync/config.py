import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    device_tracker_entity: str
    google_credentials_file: str
    google_calendar_name: str
    places_api_key: str | None
    sync_window_hours: int
    poll_interval_seconds: int


def load_config() -> Config:
    def require(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise ValueError(f"Required env var {key!r} is not set")
        return val

    return Config(
        ha_url=require("HA_URL").rstrip("/"),
        ha_token=require("HA_TOKEN"),
        device_tracker_entity=require("DEVICE_TRACKER_ENTITY"),
        google_credentials_file=os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        google_calendar_name=os.getenv("GOOGLE_CALENDAR_NAME", "Timeline"),
        places_api_key=os.getenv("PLACES_API_KEY") or None,
        sync_window_hours=int(os.getenv("SYNC_WINDOW_HOURS", "48")),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "300")),
    )
