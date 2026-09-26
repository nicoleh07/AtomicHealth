#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv from https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
command -v npm >/dev/null || { echo 'Install Node.js 22.12+ or 24 LTS first.'; exit 1; }
uv sync --project backend --python 3.12 --frozen
npm --prefix frontend ci
if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; fi
printf '\nReady. Start with: python3 scripts/dev.py\n'
