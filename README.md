# image-helper

External helper for Immich that detects stereoscopic wiggle sequences (burst-like near-duplicate frames) and exports animated GIF wigglegrams back into your library.

## How it works

1. **Index** — downloads Immich thumbnails and stores perceptual hashes (`phash`) in SQLite.
2. **Detect** — sorts assets by capture time and groups adjacent frames where `0 < phash_distance < threshold` and the time gap is within a configurable window.
3. **Export** — builds a boomerang GIF from originals, uploads it to Immich, and adds it to a `Wigglegrams` album.

Detection defaults to **dry-run** so you can tune the threshold before uploading anything.

## Requirements

- Python 3.11+
- A running Immich instance (v2 or v3)
- API key with permissions: `asset.read`, `asset.download`, `asset.upload`, `album.read`, `album.write`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# Edit .env with your Immich URL and API key
```

## Usage

```bash
# Index all images (incremental by checksum)
image-helper index

# Detect wiggle groups (dry-run, default)
image-helper detect

# Export GIFs to Immich (explicit upload)
image-helper detect --upload
# or
image-helper export

# Poll for newly updated assets
image-helper daemon --once
image-helper daemon
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `IMMICH_URL` | `http://localhost:2283/api` | Immich API base URL |
| `IMMICH_API_KEY` | — | API key |
| `HASH_DB_PATH` | `./data/hashes.sqlite3` | SQLite hash cache |
| `WIGGLE_THRESHOLD` | `10` | Max phash distance for grouping |
| `WIGGLE_TIME_WINDOW_SECONDS` | `3.0` | Max seconds between adjacent frames |
| `WIGGLE_FRAME_DURATION_MS` | `100` | GIF frame duration |
| `WIGGLE_MAX_SIZE` | `600` | Max GIF frame dimension |
| `WIGGLE_BOOMERANG` | `true` | Reverse playback in GIF |
| `WIGGLE_ALBUM_NAME` | `Wigglegrams` | Target album for exports |

## Threshold tuning

1. Run `image-helper index` on your library.
2. Run `image-helper detect` and inspect the **Avg dist** column.
3. Lower `WIGGLE_THRESHOLD` for stricter matching; raise it if bursts are missed.
4. Adjust `WIGGLE_TIME_WINDOW_SECONDS` if unrelated photos get grouped.

## Phase 2: native Immich workflow webhook

When Immich merges the webhook workflow step, enable the receiver:

```bash
pip install -e ".[webhook]"
image-helper webhook
```

Configure an Immich workflow template to POST to `http://<helper-host>:8765/webhook/immich` with optional `x-immich-webhook-secret` header matching `WEBHOOK_SECRET`.

## Architecture

```text
Immich (REST API)  <->  image-helper  <->  SQLite hash store
                              |
                         GIF upload + album
```

Heavy image work stays in this helper; Immich workflows can trigger it via webhook in Phase 2.

## License

GPL-3.0-or-later (algorithm ported from [wiggle-wiggle](https://github.com/JCLemme/wiggle-wiggle)).
