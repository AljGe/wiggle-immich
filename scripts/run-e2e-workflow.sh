#!/usr/bin/env bash
# Workflow preview E2E: Immich (preview) + image-helper webhook daemon.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_DIR="$ROOT/docker"
FIXTURES_DIR="$ROOT/testdata/fixtures"
RUNTIME_ENV="$DOCKER_DIR/test.runtime.env"
PYTHON="${PYTHON:-}"
export IMMICH_VERSION="${IMMICH_WORKFLOW_VERSION:-next}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-image-helper-e2e-secret}"

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

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker is required. Run ./scripts/run-e2e.sh first to validate Docker setup." >&2
  exit 1
fi

cd "$ROOT"

if [[ "${RESET_TEST_DATA:-1}" == "1" && -d "$DOCKER_DIR/test-data" ]]; then
  echo "==> Resetting docker/test-data for a clean Immich instance"
  docker run --rm -v "$DOCKER_DIR/test-data:/data" alpine:3.20 sh -c 'rm -rf /data/* /data/.[!.]* /data/..?*' 2>/dev/null \
    || rm -rf "$DOCKER_DIR/test-data" 2>/dev/null \
    || sudo rm -rf "$DOCKER_DIR/test-data"
fi

echo "==> Generating synthetic wiggle burst fixtures"
"$PYTHON" scripts/generate_fixtures.py "$FIXTURES_DIR"

echo "==> Starting Immich test stack (IMMICH_VERSION=${IMMICH_VERSION})"
if ! docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  up -d --wait; then
  echo "ERROR: Immich stack failed to start." >&2
  docker logs image_helper_immich_server 2>&1 | tail -40 >&2 || true
  exit 1
fi

cleanup() {
  if [[ "${KEEP_STACK:-0}" != "1" ]]; then
    echo "==> Stopping test stack (set KEEP_STACK=1 to leave it running)"
    docker compose --env-file "$DOCKER_DIR/immich.env" \
      -f "$DOCKER_DIR/compose.test.yml" \
      --project-directory "$DOCKER_DIR" \
      --profile workflow-e2e \
      down -v
  fi
}
trap cleanup EXIT

echo "==> Creating admin + API key for webhook runtime env"
"$PYTHON" scripts/e2e_bootstrap.py \
  --immich-url "http://localhost:2283/api" \
  --runtime-env "$RUNTIME_ENV" \
  --setup-only \
  --webhook-secret "$WEBHOOK_SECRET"

echo "==> Building and starting image-helper webhook daemon"
docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  --profile workflow-e2e \
  build image-helper-webhook

docker compose --env-file "$DOCKER_DIR/immich.env" \
  -f "$DOCKER_DIR/compose.test.yml" \
  --project-directory "$DOCKER_DIR" \
  --profile workflow-e2e \
  up -d --wait image-helper-webhook

echo "==> Installing workflow and uploading fixtures (workflow triggers)"
PYTHONPATH="$ROOT/scripts" "$PYTHON" scripts/e2e_workflow_bootstrap.py \
  --immich-url "http://localhost:2283/api" \
  --fixtures-dir "$FIXTURES_DIR" \
  --runtime-env "$RUNTIME_ENV" \
  --webhook-url "http://image-helper-webhook:8765/webhook/immich" \
  --webhook-secret "$WEBHOOK_SECRET" \
  --skip-admin-setup

API_KEY="$(grep '^IMMICH_API_KEY=' "$RUNTIME_ENV" | cut -d= -f2-)"

echo "==> Verifying exported GIF and album"
"$PYTHON" scripts/e2e_verify.py \
  --immich-url "http://localhost:2283/api" \
  --api-key "$API_KEY"

echo
echo "Workflow E2E test passed (IMMICH_VERSION=${IMMICH_VERSION})."
echo "Immich UI: http://localhost:2283"
echo "To inspect manually before teardown: KEEP_STACK=1 $0"
