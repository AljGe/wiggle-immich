# image-helper

External helper for Immich that detects stereoscopic wiggle sequences (burst-like near-duplicate frames) and exports animated GIF wigglegrams back into your library.

## How it works

1. **Index** — downloads Immich thumbnails and stores perceptual hashes (`phash`) in SQLite.
2. **Detect** — sorts assets by capture time and groups adjacent frames where `0 < phash_distance < threshold` and the time gap is within a configurable window.
3. **Export** — builds a boomerang GIF from originals, uploads it to Immich, and adds it to a `Wigglegrams` album.

Detection defaults to **dry-run** so you can tune the threshold before uploading anything.

## Requirements

- Python 3.11+ (or [uv](https://docs.astral.sh/uv/) for the recommended install path)
- A running Immich instance (v2 or v3)
- API key with permissions: `asset.read`, `asset.view`, `asset.download`, `asset.upload`, `album.read`, `album.create`, `albumAsset.create`

## Quick start (local)

```bash
./scripts/setup.sh
uv run image-helper doctor
uv run image-helper index
uv run image-helper detect
```

`setup.sh` runs `uv sync`, creates `.env` from `.env.example` when needed, and prints next steps. For dev dependencies (pytest):

```bash
DEV=1 ./scripts/setup.sh
```

### Manual install (without setup.sh)

```bash
uv sync
uv run image-helper config init
uv run image-helper doctor
```

**pip fallback** (no uv):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # edit IMMICH_URL and IMMICH_API_KEY
image-helper doctor
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

Settings are loaded with this precedence:

1. **Shell environment variables** — always override file values
2. **Env file** — first existing file wins: `--env-file` → `IMAGE_HELPER_ENV_FILE` → `./.env` → `~/.config/image-helper/env`
3. **Built-in defaults**

Inspect effective values at any time:

```bash
image-helper config show
```

Initialize or reset your env file:

```bash
image-helper config init
image-helper config init --output ~/.config/image-helper/env
```

| Variable | Default | Description |
|---|---|---|
| `IMMICH_URL` | `http://localhost:2283/api` | Immich API base URL |
| `IMMICH_API_KEY` | — | API key (required) |
| `HASH_DB_PATH` | `./data/hashes.sqlite3` | SQLite hash cache |
| `WIGGLE_THRESHOLD` | `10` | Max phash distance for grouping |
| `WIGGLE_TIME_WINDOW_SECONDS` | `3.0` | Max seconds between adjacent frames |
| `WIGGLE_FRAME_DURATION_MS` | `100` | GIF frame duration |
| `WIGGLE_MAX_SIZE` | `600` | Max GIF frame dimension |
| `WIGGLE_BOOMERANG` | `true` | Reverse playback in GIF |
| `WIGGLE_ALBUM_NAME` | `Wigglegrams` | Target album for exports |
| `DAEMON_POLL_INTERVAL_SECONDS` | `60` | Daemon poll interval |
| `WEBHOOK_HOST` | `0.0.0.0` | Webhook bind address |
| `WEBHOOK_PORT` | `8765` | Webhook port |
| `WEBHOOK_SECRET` | — | Optional webhook auth header |
| `DEVICE_ID` | `image-helper` | Upload device id |

Verify connectivity before indexing:

```bash
image-helper doctor
```

## Docker sidecar

Run the daemon alongside an existing Immich Docker Compose stack:

```bash
cp docker/.env.example docker/.env
# Edit IMMICH_URL and IMMICH_API_KEY
```

**Same Docker network as Immich** (most common):

```dotenv
IMMICH_URL=http://immich-server:2283/api
IMMICH_DOCKER_NETWORK=immich_default
```

Find your Immich network name with `docker network ls` (often `<project>_default`).

**Immich on the Docker host** (helper in a container, Immich on localhost):

```dotenv
IMMICH_URL=http://host.docker.internal:2283/api
```

On Linux you may need `extra_hosts: ["host.docker.internal:host-gateway"]` on the service.

Start the sidecar:

```bash
docker compose -f docker/compose.yml --project-directory docker up -d --build
```

Hash data persists in the `helper-data` volume.

## Threshold tuning

1. Run `image-helper index` on your library.
2. Run `image-helper detect` and inspect the **Avg dist** column.
3. Lower `WIGGLE_THRESHOLD` for stricter matching; raise it if bursts are missed.
4. Adjust `WIGGLE_TIME_WINDOW_SECONDS` if unrelated photos get grouped.

## Testing

### Fast unit tests (no Docker)

```bash
uv sync --group dev
uv run pytest
```

These cover detector logic, configuration, and doctor checks without Immich.

### Full contained E2E (Docker + Immich)

This spins up an isolated Immich stack, uploads synthetic wiggle burst frames, runs the full `index → detect → export` pipeline, and verifies the GIF lands in the `Wigglegrams` album.

**Requirements:** Docker with WSL2 integration enabled (Docker Desktop → Settings → Resources → WSL integration).

```bash
# One command — brings stack up, runs pipeline, tears down
./scripts/run-e2e.sh

# Keep Immich running after success for manual inspection
KEEP_STACK=1 ./scripts/run-e2e.sh
```

**What it does:**

1. `scripts/generate_fixtures.py` — creates 3 slightly-shifted PNG burst frames + 1 control image
2. `docker/compose.test.yml` — Immich (server, postgres, redis, ML) on port `2283`
3. `scripts/e2e_bootstrap.py` — creates admin, API key, uploads fixtures, waits for thumbnails
4. `image-helper` container — runs index/detect/export against `http://immich-server:2283/api`
5. `scripts/e2e_verify.py` — asserts GIF exists in the `Wigglegrams` album

**Manual steps** (if you prefer step-by-step):

```bash
python scripts/generate_fixtures.py testdata/fixtures

docker compose --env-file docker/immich.env \
  -f docker/compose.test.yml --project-directory docker up -d --wait

python scripts/e2e_bootstrap.py --fixtures-dir testdata/fixtures

docker compose --env-file docker/immich.env \
  -f docker/compose.test.yml --project-directory docker \
  --profile helper run --rm image-helper index
docker compose --env-file docker/immich.env \
  -f docker/compose.test.yml --project-directory docker \
  --profile helper run --rm image-helper export

# Immich UI: http://localhost:2283  (admin@image-helper.test / image-helper-test)
```

Test data is stored under `docker/test-data/` and removed on teardown (`docker compose down -v`).

## Phase 2: native Immich workflow webhook

When Immich merges the webhook workflow step, enable the receiver:

```bash
uv sync --extra webhook
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
