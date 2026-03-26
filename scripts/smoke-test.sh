#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${1:-}"

echo "[smoke] root: $ROOT_DIR"

if [[ ! -x "./run_enrich.sh" ]]; then
  echo "[smoke][error] run_enrich.sh not found or not executable."
  exit 1
fi

if [[ ! -x "./.venv/bin/python" ]]; then
  echo "[smoke][error] .venv is missing. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate

python -m event_leads process --help >/dev/null
python -m event_leads init-config --help >/dev/null
echo "[smoke] cli commands: ok"

python - <<'PY'
import os, sys
key = os.getenv("OPENAI_API_KEY", "")
if not key:
    print("[smoke][warn] OPENAI_API_KEY is not set; live LLM run will fail.")
    sys.exit(0)
if any(ord(ch) > 127 for ch in key):
    print("[smoke][error] OPENAI_API_KEY contains non-ASCII characters.")
    sys.exit(1)
base = os.getenv("OPENAI_BASE_URL", "")
if base and any(ord(ch) > 127 for ch in base):
    print("[smoke][error] OPENAI_BASE_URL contains non-ASCII characters.")
    sys.exit(1)
print("[smoke] openai env vars: ok")
PY

if [[ -n "$CONFIG_PATH" ]]; then
  if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "[smoke][error] config not found: $CONFIG_PATH"
    exit 1
  fi

  python - "$CONFIG_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

cfg_path = Path(sys.argv[1]).resolve()
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

for key in ("event", "sources", "output"):
    if key not in cfg:
        raise SystemExit(f"[smoke][error] missing top-level key: {key}")

if not cfg.get("sources"):
    raise SystemExit("[smoke][error] sources is empty")

data_dir = (cfg_path.parent / cfg.get("data_dir", ".")).resolve()
if not data_dir.exists():
    raise SystemExit(f"[smoke][error] data_dir does not exist: {data_dir}")

print("[smoke] config structure: ok")
print(f"[smoke] resolved data_dir: {data_dir}")
PY
fi

echo "[smoke] all checks passed."
