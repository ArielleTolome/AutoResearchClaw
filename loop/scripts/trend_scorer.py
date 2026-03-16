#!/usr/bin/env python3
"""
trend_scorer.py — 7-day rolling trend momentum scorer for AutoResearchClaw.
Queries Qdrant for recent signals, groups by topic, assigns momentum scores,
and optionally posts a weekly "What's Heating Up" card to Discord.

Usage:
  python trend_scorer.py                          # Print trending topics (7 days)
  python trend_scorer.py --vertical Medicare       # Filter by vertical
  python trend_scorer.py --days 14                 # Custom window
  python trend_scorer.py --weekly-card             # Post Discord embed with top 5
"""

import os, sys, json, argparse, datetime, re
from collections import defaultdict
from pathlib import Path

import requests
import yaml

try:
    from fuzzywuzzy import fuzz
    HAS_FUZZY = True
except ImportError:
    HAS_FUZZY = False

from qdrant_sink import get_signals_since, QDRANT_ENABLED

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
INTEL_WEBHOOK = (
    CFG.get("discord", {}).get("intel_webhook_url")
    or os.getenv("INTEL_WEBHOOK_URL", "")
)

# ── Topic normalization ──────────────────────────────────────────────────────

def _normalize_topic(headline: str) -> str:
    """Extract a rough topic slug from a headline for grouping."""
    text = headline.lower().strip()
    # Remove common noise words
    for noise in ["breaking:", "update:", "report:", "new:", "exclusive:"]:
        text = text.replace(noise, "")
    # Take first ~6 meaningful words
    words = re.findall(r'[a-z]+', text)
    stop = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
            "for", "of", "and", "or", "but", "with", "from", "by", "that", "this",
            "its", "has", "have", "had", "will", "can", "could", "would", "should",
            "may", "be", "been", "do", "does", "did", "not", "no", "so", "if",
            "how", "what", "when", "where", "who", "why", "which", "their", "your",
            "our", "my", "his", "her", "all", "more", "some", "any", "each", "every",
            "about", "after", "before", "over", "under", "between", "through", "into"}
    meaningful = [w for w in words if w not in stop and len(w) > 2]
    return " ".join(meaningful[:6])


def _cluster_topics(signals: list[dict]) -> dict[str, list[dict]]:
    """Group signals by normalized topic using fuzzy matching."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    cluster_keys: list[str] = []

    for signal in signals:
        headline = signal.get("headline") or signal.get("title", "")
        topic = _normalize_topic(headline)
        if not topic:
            continue

        # Try to match to existing cluster
        matched = False
        if HAS_FUZZY:
            for existing_key in cluster_keys:
                if fuzz.token_sort_ratio(topic, existing_key) > 65:
                    clusters[existing_key].append(signal)
                    matched = True
                    break

        if not matched:
            # Simple keyword overlap fallback
            topic_words = set(topic.split())
            for existing_key in cluster_keys:
                existing_words = set(existing_key.split())
                overlap = len(topic_words & existing_words)
                if overlap >= 2 and overlap / max(len(topic_words), len(existing_words)) > 0.4:
                    clusters[existing_key].append(signal)
                    matched = True
                    break

        if not matched:
            cluster_keys.append(topic)
            clusters[topic].append(signal)

    return dict(clusters)


def _momentum_label(count: int) -> str:
    if count >= 4:
        return "Trending 🔥"
    elif count >= 2:
        return "Rising 📈"
    else:
        return "Stable"


# ── Main logic ───────────────────────────────────────────────────────────────

def score_trends(days: int = 7, vertical: str = None) -> list[dict]:
    """Compute trend momentum for recent signals. Returns ranked list."""
    signals = get_signals_since(days=days, vertical=vertical)

    if not signals:
        print(f"[trend] No signals found for last {days} days" +
              (f" (vertical={vertical})" if vertical else ""))
        return []

    clusters = _cluster_topics(signals)

    ranked = []
    for topic, sigs in clusters.items():
        count = len(sigs)
        sample = sigs[0].get("headline", "")[:120]
        verticals = list(set(s.get("vertical", "Unknown") for s in sigs))
        ranked.append({
            "topic": topic,
            "count": count,
            "momentum": _momentum_label(count),
            "sample_headline": sample,
            "verticals": verticals,
        })

    ranked.sort(key=lambda x: x["count"], reverse=True)
    return ranked


def post_weekly_card(ranked: list[dict]):
    """Post 'What's Heating Up' embed to Discord via intel webhook."""
    if not INTEL_WEBHOOK:
        print("[trend] No intel_webhook_url configured — cannot post weekly card")
        return

    top5 = ranked[:5]
    if not top5:
        print("[trend] No trending topics to post")
        return

    lines = []
    for i, t in enumerate(top5, 1):
        emoji = "🔥" if t["count"] >= 4 else "📈" if t["count"] >= 2 else "•"
        lines.append(
            f"**{i}. {t['topic'][:50]}** {emoji}\n"
            f"   {t['count']} signals · {t['momentum']}\n"
            f"   _\"{t['sample_headline'][:80]}\"_"
        )

    embed = {
        "title": "🔥 What's Heating Up — Weekly Trend Report",
        "description": "\n\n".join(lines),
        "color": 0xE74C3C,
        "footer": {"text": "AutoResearchClaw v2.0 · trend_scorer.py"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        r = requests.post(INTEL_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 204):
            print(f"[trend] Weekly card posted with {len(top5)} trending topics")
        else:
            print(f"[trend] Discord post failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"[trend] Discord post error: {e}")


def print_summary(ranked: list[dict]):
    """Print human-readable trend summary."""
    if not ranked:
        print("\nNo trending topics found.")
        return

    print(f"\n{'='*60}")
    print(f"  TREND MOMENTUM REPORT")
    print(f"{'='*60}")
    for i, t in enumerate(ranked[:15], 1):
        print(f"\n  {i}. {t['topic'][:50]}")
        print(f"     Count: {t['count']}  |  Momentum: {t['momentum']}")
        print(f"     Verticals: {', '.join(t['verticals'][:3])}")
        print(f"     Sample: \"{t['sample_headline'][:80]}\"")
    print(f"\n{'='*60}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trend momentum scorer for AutoResearchClaw signals")
    parser.add_argument("--vertical", help="Filter by vertical (e.g. Medicare)")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    parser.add_argument("--weekly-card", action="store_true", help="Post weekly 'What's Heating Up' card to Discord")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    if not QDRANT_ENABLED:
        print("[trend] Qdrant is disabled in config. Set qdrant.enabled: true")
        sys.exit(0)

    ranked = score_trends(days=args.days, vertical=args.vertical)

    if args.json:
        print(json.dumps(ranked, indent=2))
    else:
        print_summary(ranked)
        # Also print JSON
        print("JSON output:")
        print(json.dumps(ranked, indent=2))

    if args.weekly_card:
        post_weekly_card(ranked)


if __name__ == "__main__":
    main()
