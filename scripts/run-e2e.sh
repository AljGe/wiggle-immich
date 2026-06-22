#!/usr/bin/env bash
# One-command contained E2E test: Immich + image-helper in Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$ROOT/docker"
FIXTURES_DIR="$ROOT/testdata/fixtures"
RUNTIME_ENV="$DOCKER_DIR/test.runtime.env"
PYTHON="${PYTHON:-}"

if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  else
  echo "No Python interpreter found. Create .venv or set PYTHON=..." >&2
  exit 1
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Enable Docker Desktop WSL integration, then retry." >&2
  exit 1
fi

cd "$ROOT"

echo "==> Generating synthetic wiggle burst fixtures"
"$PYTHON" scripts/generate_fixtures.py "$FIXTURES_DIR"

echo "==> Starting isolated Immich test stack"
docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  up -d --wait

cleanup() {
  if [[ "${KEEP_STACK:-0}" != "1" ]]; then
    echo "==> Stopping test stack (set KEEP_STACK=1 to leave it running)"
    docker compose --env-file "$DOCKER_DIR/immich.env" \
      -f "$DOCKER_DIR/compose.test.yml" \
      --project-directory "$DOCKER_DIR" \
      down -v
  fi
}
trap cleanup EXIT

echo "==> Bootstrapping Immich (admin, API key, fixture upload)"
"$PYTHON" scripts/e2e_bootstrap.py \
  --immich-url "http://localhost:2283/api" \
  --fixtures-dir "$FIXTURES_DIR" \
  --runtime-env "$RUNTIME_ENV"

API_KEY="$(grep '^IMMICH_API_KEY=' "$RUNTIME_ENV" | cut -d= -f2-)"

echo "==> Building image-helper container"
docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  build image-helper

echo "==> Running image-helper pipeline"
docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  --profile helper run --rm image-helper index
docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  --profile helper run --rm image-helper detect
docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  --profile helper run --rm image-helper export

echo "==> Verifying exported GIF and album"
"$PYTHON" scripts/e2e_verify.py \
  --immich-url "http://localhost:2283/api" \
  --api-key "$API_KEY"

echo
echo "E2E test passed."
echo "Immich UI: http://localhost:2283"
echo "To inspect manually before teardown: KEEP_STACK=1 $0"
