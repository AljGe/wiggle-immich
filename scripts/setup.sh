#!/usr/bin/env bash
# Idempotent local setup: uv sync, optional dev extras, config init when needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'EOF'
uv is not installed.

Install it with:
  curl -LsSf https://astral.sh/uv/install.sh | sh

Then re-run:
  ./scripts/setup.sh
EOF
  exit 1
fi

SYNC_ARGS=(sync)
if [[ "${DEV:-0}" == "1" ]]; then
  SYNC_ARGS+=(--group dev)
fi

echo "==> Installing dependencies (uv ${SYNC_ARGS[*]})"
uv "${SYNC_ARGS[@]}"

has_env_file() {
  [[ -f .env ]] \
    || [[ -n "${IMAGE_HELPER_ENV_FILE:-}" && -f "${IMAGE_HELPER_ENV_FILE}" ]] \
    || [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/image-helper/env" ]]
}

if ! has_env_file; then
  echo "==> No env file found; running config init"
  uv run image-helper config init
else
  echo "==> Env file already present; skipping config init"
fi

cat <<'EOF'

Setup complete.

Next steps:
  uv run image-helper doctor
  uv run image-helper index
  uv run image-helper detect

Or activate the venv and use the CLI directly:
  source .venv/bin/activate
  image-helper doctor
EOF
