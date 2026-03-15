#!/bin/bash
# AutoResearchClaw launcher
# Usage: ./research.sh "Your research topic here"
#        ./research.sh "Your topic" --no-auto-approve  (stops at gates for human review)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -z "$1" ]; then
  echo "Usage: ./research.sh \"Your research topic\""
  exit 1
fi

TOPIC="$1"
shift

# Parse --config and --no-auto-approve from remaining args
CONFIG="config.arc.yaml"
AUTO_APPROVE="--auto-approve"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config|-c)
      CONFIG="$2"
      shift 2
      ;;
    --no-auto-approve)
      AUTO_APPROVE=""
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

# Load .env if present
[ -f .env ] && source .env

source .venv/bin/activate

echo "🔬 Starting AutoResearchClaw..."
echo "📝 Topic: $TOPIC"
echo "⚙️  Config: $CONFIG"
echo ""

researchclaw run \
  --config "$CONFIG" \
  --topic "$TOPIC" \
  $AUTO_APPROVE \
  "${EXTRA_ARGS[@]}"
