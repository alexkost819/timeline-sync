# timeline-sync

Syncs your Home Assistant location history to a dedicated Google Calendar — one event per place visit. Home Assistant is the source of truth; Calendar is write-only.

## How it works

1. Reads state history from Home Assistant's REST API (works with `device_tracker.*` and `sensor.*_geocoded_location` entities)
2. Groups consecutive same-place states into **Visits**
3. Enriches place names via a priority chain (see below)
4. Diffs against the "Timeline" Google Calendar (using event extended properties as keys)
5. Creates, updates, or deletes events to match HA state

Each event stores a deterministic `ha_visit_id` in its extended properties. Re-running is fully idempotent.

### Place name enrichment chain

For each visit, the best available name is resolved in this order (first match wins):

1. **HA zone name** — if the entity state is a known zone slug (e.g. `home` → `"Home"`)
2. **Google Contact** — if the geocoded address fuzzy-matches a contact's saved address (e.g. `"Dan Smith's Home"`)
3. **Sensor fusion** — if GPS coordinates fall within an HA zone boundary, use that zone's friendly name
4. **known_names cache** — reuses a Places API result already stored in an existing Calendar event
5. **Google Places API** — nearest business/establishment (optional, paid); up to 3 candidates — runner-up names go in the event description
6. **Geocoded address** — HA companion app's reverse-geocoded address, formatted (e.g. `"1103 Fairwood Ave, Sunnyvale, CA 94089, USA"`)

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
4. Search for **"People API"** → click it → click **Enable** (used for Google Contacts address matching)
5. *(Optional, for place name enrichment)* Search for **"Places API"** → click it → click **Enable**

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

Find your entity ID in Developer Tools → States. Works with `device_tracker.*` entities or sensor entities like `sensor.phone_geocoded_location`.

### 4. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```
HA_URL=http://homeassistant.local:8123
HA_TOKEN=your_token_here
# Any HA entity whose state changes represent location: device_tracker.*, sensor.*_geocoded_location, etc.
HA_ENTITY=device_tracker.your_phone

GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_CALENDAR_NAME=Timeline

# Optional — enables place name lookup for locations outside your HA zones
PLACES_API_KEY=your_google_places_api_key
PLACES_DAILY_LIMIT=50

# Google Contacts — labels locations with contact names (e.g. "Dan's Home")
# Contacts are fetched from Google People API and cached locally
CONTACTS_REFRESH_HOURS=24
CONTACTS_CACHE_FILE=contacts_cache.json

SYNC_WINDOW_HOURS=48
# Visits (and ongoing visits not yet this long) shorter than this are ignored
MIN_VISIT_MINUTES=10
```

### 5. Generate token.pickle (one-time OAuth)

The tool needs a `token.pickle` file containing your Google OAuth credentials. This must be created on a machine with a browser (e.g. your Mac) before deploying headless.

```bash
uv run timeline-sync --once
```

A browser window opens for Google OAuth authorization. After you approve, the token is saved to `token.pickle`. Subsequent runs (including Docker) reuse and auto-refresh this token.

> **Re-auth required if you added Contacts support later:** The OAuth token is scoped at creation time. If you already have a `token.pickle` but contacts aren't loading (you'll see a `403` warning in logs), delete the old token and re-authorize:
> ```bash
> rm token.pickle && uv run timeline-sync --once
> # After browser approval, copy the new token to your LXC:
> scp token.pickle root@<LXC_IP>:/opt/timeline-sync/
> ```

### 6. Run

**Dry run** (no Calendar writes, prints derived visits):
```bash
uv run timeline-sync --dry-run
```

**Single sync and exit:**
```bash
uv run timeline-sync --once
```

**Event-driven** (connects to HA WebSocket, syncs on every location state change):
```bash
uv run timeline-sync
```

## Calendar events

Events appear on a dedicated calendar named "Timeline" (configurable). Each event:

- **Title:** `Home`, `Office`, `Starbucks`, or `Dan Smith's Home` (contact's full name + place type)
- **Location:** Geocoded address from HA companion app (clickable link to Google Maps)
- **Description:** Alternative place name candidates only (e.g. `"Other options: Subway, Chipotle"`); omitted if Places API returned only one match or wasn't used
- **Times:** Exact entry/exit times from HA
- Consecutive visits to the same place are merged into a single event
- Ongoing visits show with the current time as end (updated each sync); the event is not updated if only the duration changed

## HA zones and unknown places

- **HA zones** (home, work, etc.) → use the zone's `friendly_name` from HA
- **`sensor.*_geocoded_location` entity** → state is the raw address string; enriched through the full priority chain (contact → sensor fusion → Places API → formatted address)
- **Sensor fusion** → if GPS coordinates from the sensor fall within a zone's radius, the zone's friendly name is used (requires `latitude`, `longitude`, and `radius` attributes on the HA zone entity; defaults to 100 m if `radius` is missing)
- **Address matches a Google Contact** → title replaced with contact label, e.g. `Dan Smith's Home` or `Jane Doe's Work`
- **Places API configured** → nearest establishment (up to 3 candidates; runner-up names appear in description)
- **Quota exhausted or no Places API key** → falls back to formatted geocoded address
- **No enrichment available** → left as-is

Contacts are fetched from Google People API at startup and refreshed every `CONTACTS_REFRESH_HOURS` hours. If a contact has a home or work address that fuzzy-matches a geocoded location, the event title is replaced with the contact's full display name + location type (e.g. `"Dan Smith's Home"`, `"Jane Doe's Work"`, `"Bob Jones's Other Address"`).

To add a new known zone, define it in Home Assistant and it will appear automatically on the next sync.

## Docker deployment (Proxmox)

### 1. Create a Docker LXC on Proxmox

From the Proxmox shell, run the community Helper Script to create a Docker LXC:

```bash
bash -c "$(wget -qLO - https://github.com/community-scripts/ProxmoxVE/raw/main/ct/docker.sh)"
```

Default specs (2 CPU, 2GB RAM, 8GB disk) are sufficient.

### 2. Generate token.pickle on your Mac (one-time)

Before deploying, create `token.pickle` locally where a browser is available:

```bash
cd /path/to/timeline-sync
uv run timeline-sync --once
# Browser opens → approve OAuth → token.pickle created
```

> **Note:** `--dry-run` skips credential loading entirely and will NOT create `token.pickle`. Use `--once`.

### 3. Deploy to the LXC

```bash
# On your Mac — clone repo and copy secrets to LXC (replace <LXC_IP>)
git clone https://github.com/akost819/timeline-sync /opt/timeline-sync
scp credentials.json token.pickle root@<LXC_IP>:/opt/timeline-sync/

# On the LXC
cd /opt/timeline-sync
cp .env.example .env   # fill in real values
docker compose up -d
docker compose logs -f  # verify WebSocket connected and initial sync ran
```

### 4. Verify

1. `docker compose logs -f` — confirm "WebSocket connected" and "Sync complete"
2. Move phone to a different HA zone — Calendar event should appear within seconds
3. Check Google Calendar "Timeline" for events without `@` prefix
4. `docker volume inspect timeline-sync_quota` — confirms quota counter persists across restarts

Log rotation is handled automatically by Docker's `json-file` driver (10MB × 3 files per container, capped at 30MB).

## Development

```bash
uv sync --extra dev
uv run pytest -v        # tests
uv format               # format
uv run ty check src/    # type check
```

### Project structure

```
src/timeline_sync/
  config.py             # env var config
  ha_reader.py          # HA REST API client
  visit_deriver.py      # state history → Visit dataclasses (pure)
  place_resolver.py     # zone names + Places API enrichment + address formatting
  contact_resolver.py   # Google People API contact address matching
  quota.py              # per-visit-date Places API call budget
  calendar_sync.py      # Google Calendar diff + apply
  main.py               # entry point, WebSocket event loop

tests/
  test_visit_deriver.py
  test_place_resolver.py
  test_contact_resolver.py
  test_calendar_sync.py
  test_quota.py
```

### Key invariants (for Claude)

- `visit_id` is `sha256(entity_id|place_name|start_time_iso)[:16]` — changes when zone name or start time changes
- `visit_id` stored in Calendar event `extendedProperties.private.ha_visit_id` — this is the deduplication key
- No local database. Calendar state is the only external state store (beyond HA itself)
- All sync logic flows: HA history → derive visits → enrich → merge consecutive → diff calendar → apply
- `dry_run=True` in `CalendarSync.sync()` counts operations without executing them
- `_format_address()` in `place_resolver.py` normalizes geocoded addresses (title-case, 2-char alpha words uppercased as state codes, solo 3-char alpha words uppercased as country codes); imported by `calendar_sync.py` for the `location` field
- Google Calendar `privateExtendedProperty` filter requires exact `key=value` — wildcards are not supported; `_fetch_events_in_window` fetches all events in the window and filters client-side
- HA history API requires `no_attributes=false` and must NOT include `minimal_response=true` — the latter strips all attributes regardless of `no_attributes`
- Contact resolver uses Google People API (not local contacts); requires `contacts.readonly` OAuth scope; `403 PERMISSION_DENIED` in logs means the People API is not enabled in your GCP project (not a re-auth issue — enable it at console.cloud.google.com/apis/library/people.googleapis.com)
- Sensor fusion in `PlaceResolver._zone_for_coords()` uses haversine distance; zone `radius` attribute (metres) controls the boundary; defaults to 100 m if missing

### Running tests

```bash
uv run pytest -v
```

Tests mock all external APIs (HA, Google Calendar, Places). No credentials needed to run tests.
