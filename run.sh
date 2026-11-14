#!/usr/bin/env bash
# Start the Aniimo draft bot (macOS / Linux). First run creates a virtual environment.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -e .
fi
if [ ! -f config.yaml ]; then
  ./.venv/bin/aniimo-pvp init
  echo "Fill in config.yaml and .env, then run ./run.sh again."
  exit 1
fi
exec ./.venv/bin/aniimo-pvp "${@:-run}"
