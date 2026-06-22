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
  echo "Docker CLI not found. Install Docker or enable Docker Desktop WSL integration." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  wsl_distro="${WSL_DISTRO_NAME:-archlinux}"

  if [[ -d /mnt/wsl/docker-desktop ]]; then
    cat >&2 <<EOF
Docker Desktop is running, but WSL integration is not active in this distro.

Your WSL distro appears to be: ${wsl_distro:-archlinux}

Fix:
  1. Open Docker Desktop on Windows
  2. Settings -> Resources -> WSL integration
  3. Enable integration for "${wsl_distro:-archlinux}"
  4. Click "Apply & restart"
  5. Close this terminal and open a new one
  6. Verify: ls -la /var/run/docker.sock && docker info

If the distro is missing from the list, run "wsl -l -v" in PowerShell
to confirm the exact name Docker Desktop should show.

EOF
  else
    cat >&2 <<'EOF'
Docker daemon is not reachable from this WSL distro.

The CLI is installed, but /var/run/docker.sock is missing. Pick one fix:

A) Docker Desktop (common on WSL2)
   1. Start Docker Desktop on Windows
   2. Settings -> Resources -> WSL integration
   3. Enable integration for this distro
   4. Restart this WSL terminal and run: docker info

B) Native Docker inside WSL (Arch)
   sudo pacman -S docker docker-compose
   sudo systemctl enable --now docker
   sudo usermod -aG docker "$USER"   # then log out/in
   docker info

EOF
  fi
  echo "After docker info works, re-run: ./scripts/run-e2e.sh" >&2
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
