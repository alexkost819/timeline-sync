# timeline-sync

Syncs your Home Assistant location history to a dedicated Google Calendar — one event per place visit. Home Assistant is the source of truth; Calendar is write-only.

## How it works

1. Reads `device_tracker` state history from Home Assistant's REST API
2. Groups consecutive same-zone states into **Visits**
3. Enriches unknown locations via Google Places API (optional)
4. Diffs against the "Timeline" Google Calendar (using event extended properties as keys)
5. Creates, updates, or deletes events to match HA state

Each event stores a deterministic `ha_visit_id` in its extended properties. Re-running is fully idempotent.

## Setup

### Prerequisites

- Home Assistant with companion app configured on Android (device tracker entity enabled)
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- A Google Cloud project with the Calendar API enabled

### 1. Clone and install

```bash
git clone https://github.com/akost/timeline-sync
cd timeline-sync
uv sync
```

### 2. Google Cloud project setup

#### Enable APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and select or create a project
2. In the left sidebar: **APIs & Services → Library**
3. Search for **"Google Calendar API"** → click it → click **Enable**
4. *(Optional, for place name enrichment)* Search for **"Places API"** → click it → click **Enable**

#### Create OAuth credentials

1. In the left sidebar: **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. If prompted to configure the consent screen first:
   - Click **Configure Consent Screen** → choose **External** → fill in app name (e.g. "timeline-sync") and your email → save
   - Under **Scopes**: no changes needed (Calendar scope is requested at runtime)
   - Under **Test users**: add your Google account email → save
4. Back in Credentials → **+ Create Credentials → OAuth client ID**
5. Application type: **Desktop app** → name it anything (e.g. "timeline-sync") → click **Create**
6. Click **Download JSON** on the newly created credential → save as `credentials.json` in the project root

#### Get a Places API key *(optional)*

1. In the left sidebar: **APIs & Services → Credentials**
2. Click **+ Create Credentials → API key**
3. Copy the key → set it as `PLACES_API_KEY` in your `.env`
4. *(Recommended)* Click the key → under **API restrictions**, restrict it to **Places API** only

### 3. Home Assistant token

In Home Assistant: Profile → Long-Lived Access Tokens → Create token

Find your device tracker entity ID in Developer Tools → States (search for `device_tracker`).

### 4. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```
HA_URL=http://homeassistant.local:8123
HA_TOKEN=your_token_here
DEVICE_TRACKER_ENTITY=device_tracker.your_phone

GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_CALENDAR_NAME=Timeline

# Optional — enables place name lookup for locations outside your HA zones
PLACES_API_KEY=your_google_places_api_key

SYNC_WINDOW_HOURS=48
POLL_INTERVAL_SECONDS=300
```

### 5. Run

**Dry run** (no Calendar writes, prints derived visits):
```bash
uv run timeline-sync --dry-run
```

**Single sync and exit:**
```bash
uv run timeline-sync --once
```

**Continuous polling** (every `POLL_INTERVAL_SECONDS`):
```bash
uv run timeline-sync
```

On first run, a browser window will open for Google OAuth authorization. The token is saved to `token.pickle` for subsequent runs.

## Calendar events

Events appear on a dedicated calendar named "Timeline" (configurable). Each event:

- **Title:** `@ Home`, `@ Office`, `@ Starbucks`
- **Times:** Exact entry/exit times from HA
- **Description:** GPS coordinates and data source
- Ongoing visits show with the current time as end (updated each sync)

## HA zones and unknown places

- **HA zones** (home, work, etc.) → use the zone's `friendly_name` from HA
- **`not_home` with Places API configured** → nearest establishment via Google Places
- **`not_home` without Places API** → left as-is (no event title enrichment)

To add a new known place, define a zone in Home Assistant and it will automatically appear with the right name on the next sync.

## Docker

```bash
docker build -t timeline-sync .
docker run -d \
  --env-file .env \
  -v $(pwd)/credentials.json:/app/credentials.json \
  -v $(pwd)/token.pickle:/app/token.pickle \
  timeline-sync
```

## Development

```bash
uv sync --extra dev
uv run pytest -v
```

### Project structure

```
src/timeline_sync/
  config.py          # env var config
  ha_reader.py       # HA REST API client
  visit_deriver.py   # state history → Visit dataclasses (pure)
  place_resolver.py  # zone names + Places API enrichment
  calendar_sync.py   # Google Calendar diff + apply
  main.py            # entry point, polling loop

tests/
  test_visit_deriver.py
  test_place_resolver.py
  test_calendar_sync.py
```

### Key invariants (for Claude)

- `visit_id` is `sha256(entity_id|place_name|start_time_iso)[:16]` — changes when zone name or start time changes
- `visit_id` stored in Calendar event `extendedProperties.private.ha_visit_id` — this is the deduplication key
- No local database. Calendar state is the only external state store (beyond HA itself)
- All sync logic flows: HA history → derive visits → enrich → diff calendar → apply
- `dry_run=True` in `CalendarSync.sync()` counts operations without executing them

### Running tests

```bash
uv run pytest -v
```

Tests mock all external APIs (HA, Google Calendar, Places). No credentials needed to run tests.
