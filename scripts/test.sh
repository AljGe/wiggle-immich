#!/usr/bin/env bash
# Run unit tests from the repository root (works from any cwd).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec uv run pytest "$@"
