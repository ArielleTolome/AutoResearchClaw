#!/usr/bin/env bash
# run_brief.sh — triggered by Rachel via Discord
# Usage: ./run_brief.sh "<topic>" [platform] [run_id]
# Platforms: meta (default) | tiktok | youtube | native

set -euo pipefail

TOPIC="${1:-}"; PLATFORM="${2:-meta}"; RUN_ID="${3:-}";

if [[ -z "$TOPIC" ]]; then echo "ERROR: topic required" >&2; exit 1; fi

WORKDIR="/root/AutoResearchClaw"
cd "$WORKDIR"

# Pick config file
case "$PLATFORM" in
  tiktok)  CONFIG="config.arc.ads.tiktok.yaml" ;;
  youtube) CONFIG="config.arc.ads.youtube.yaml" ;;
  native)  CONFIG="config.arc.ads.native.yaml" ;;
  *)       CONFIG="config.arc.ads.yaml" ;;
esac

# Patch topic into config inline (temp file)
TMPCONFIG="/tmp/arc_brief_$$.yaml"
.venv/bin/python3 - "$CONFIG" "$TOPIC" "$TMPCONFIG" << 'PY'
import sys, yaml
with open(sys.argv[1]) as f: cfg = yaml.safe_load(f)
cfg['research']['topic'] = sys.argv[2]
with open(sys.argv[3], 'w') as f: yaml.dump(cfg, f, default_flow_style=False)
PY

# Generate run ID
if [[ -z "$RUN_ID" ]]; then RUN_ID="brief-$(date +%Y%m%d-%H%M%S)-${PLATFORM}"; fi

LOG="/root/arc_runs/${RUN_ID}.log"
DONE_MARKER="/root/arc_runs/${RUN_ID}.done"
FAIL_MARKER="/root/arc_runs/${RUN_ID}.fail"
mkdir -p /root/arc_runs

# Print run ID so caller knows what to poll
echo "$RUN_ID"

# Run in background
nohup bash -c "
  cd $WORKDIR
  bash research.sh '$TOPIC' --config '$TMPCONFIG' --run-id '$RUN_ID' > '$LOG' 2>&1
  EXIT=\$?
  rm -f '$TMPCONFIG'
  if [[ \$EXIT -eq 0 ]]; then
    touch '$DONE_MARKER'
  else
    echo \$EXIT > '$FAIL_MARKER'
  fi
" > /dev/null 2>&1 &

echo "PID $!"
