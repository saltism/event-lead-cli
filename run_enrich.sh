#!/bin/bash
# Run Event Lead CLI with enrichment.
# Usage:
#   ./run_enrich.sh configs/event-template.yaml
#   ./run_enrich.sh configs/event-template.yaml --resume
# Requires OPENAI_API_KEY to be set in your shell before running.

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: ./run_enrich.sh <config.yaml> [--resume] [-o output_dir]"
  exit 1
fi

CONFIG_PATH="$1"
shift

cd "$(dirname "$0")"
source .venv/bin/activate
python -m event_leads process "$CONFIG_PATH" --enrich "$@"
