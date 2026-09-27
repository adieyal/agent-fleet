#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run --frozen python -m scripts.checks.phase3_gate "$@"
